"""Tests for the pure-numpy DSP engine (app.render.audio.dsp)."""

import numpy as np
import pytest

from app.render.audio import dsp
from app.render.audio.dsp import SR


def _rms_db(x: np.ndarray) -> float:
    return 20.0 * np.log10(float(np.sqrt(np.mean(np.square(x)))) + 1e-12)


# ---------------------------------------------------------------- ambience


def test_synth_ambience_deterministic_per_seed() -> None:
    a = dsp.synth_ambience(["room_tone", "rain"], 2.0, seed=42)
    b = dsp.synth_ambience(["room_tone", "rain"], 2.0, seed=42)
    c = dsp.synth_ambience(["room_tone", "rain"], 2.0, seed=43)
    np.testing.assert_array_equal(a, b)
    assert not np.array_equal(a, c)


def test_synth_ambience_length_exact() -> None:
    for duration_s in (0.5, 2.0, 3.25):
        out = dsp.synth_ambience(["wind"], duration_s, seed=1)
        assert len(out) == round(duration_s * SR)
        assert out.dtype == np.float32


def test_synth_ambience_peak_normalized() -> None:
    out = dsp.synth_ambience(["room_tone", "waves_distant", "gulls"], 3.0, seed=7)
    assert np.max(np.abs(out)) == pytest.approx(0.5, abs=1e-3)


def test_synth_ambience_all_vocabulary_tags() -> None:
    tags = [
        "room_tone", "outdoor_air", "wind", "wind_trees", "waves_distant",
        "water_lapping", "gulls", "rain", "thunder_distant", "night_crickets",
        "crowd_murmur", "birds", "traffic", "city_hum", "daytime_ambience",
        "rope_creak", "glassware",
    ]
    for tag in tags:
        out = dsp.synth_ambience([tag], 1.0, seed=3)
        assert len(out) == SR
        assert np.all(np.isfinite(out))


def test_synth_ambience_unknown_tag_falls_back() -> None:
    out = dsp.synth_ambience(["totally_unknown"], 1.0, seed=3)
    assert len(out) == SR
    assert np.max(np.abs(out)) > 0.0


# ---------------------------------------------------------------- looping


def test_loop_to_length_exact_and_smooth_seam() -> None:
    rng = np.random.default_rng(0)
    # Smooth bed (low-passed noise) so a seam discontinuity would stand out.
    bed = dsp.low_pass(rng.standard_normal(3 * SR).astype(np.float32), 400.0)
    bed /= np.max(np.abs(bed))
    target = 8 * SR
    out = dsp.loop_to_length(bed, target, crossfade_s=1.0)
    assert len(out) == target
    max_step_bed = float(np.max(np.abs(np.diff(bed))))
    max_step_out = float(np.max(np.abs(np.diff(out))))
    # Equal-power crossfade: adjacent-sample jumps stay comparable to the source.
    assert max_step_out < 2.5 * max_step_bed


def test_loop_to_length_truncates_when_longer() -> None:
    bed = np.ones(SR, dtype=np.float32)
    out = dsp.loop_to_length(bed, SR // 2)
    assert len(out) == SR // 2
    np.testing.assert_array_equal(out, bed[: SR // 2])


# ---------------------------------------------------------------- placement


def test_place_clips_sample_exact_positions() -> None:
    clip_a = np.full(240, 0.25, dtype=np.float32)  # 10 ms
    clip_b = np.full(480, -0.5, dtype=np.float32)  # 20 ms
    total_ms = 100
    out = dsp.place_clips([(clip_a, 0), (clip_b, 50)], total_ms)
    assert len(out) == round(total_ms * SR / 1000)
    start_b = round(50 * SR / 1000)
    np.testing.assert_array_equal(out[:240], clip_a)
    np.testing.assert_array_equal(out[240:start_b], np.zeros(start_b - 240, dtype=np.float32))
    np.testing.assert_array_equal(out[start_b : start_b + 480], clip_b)
    np.testing.assert_array_equal(
        out[start_b + 480 :], np.zeros(len(out) - start_b - 480, dtype=np.float32)
    )


def test_place_clips_truncates_overflow() -> None:
    clip = np.ones(SR, dtype=np.float32)
    out = dsp.place_clips([(clip, 900)], 1000)  # clip extends past total
    assert len(out) == SR  # 1000 ms
    assert out[-1] == 1.0


# ---------------------------------------------------------------- ducking


def test_duck_reduces_ambience_under_speech() -> None:
    n = 4 * SR
    rng = np.random.default_rng(5)
    ambience = (0.3 * rng.standard_normal(n)).astype(np.float32)
    speech = np.zeros(n, dtype=np.float32)
    t = np.arange(SR, dtype=np.float32) / SR
    speech[SR : 2 * SR] = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    ducked = dsp.duck(ambience, speech)

    # Compare well inside the speech region (past attack) vs before speech.
    quiet_rms = _rms_db(ducked[: SR // 2])
    speech_rms = _rms_db(ducked[int(1.2 * SR) : int(1.8 * SR)])
    assert quiet_rms - speech_rms > 6.0


def test_duck_leaves_silent_regions_untouched() -> None:
    n = 2 * SR
    ambience = np.full(n, 0.2, dtype=np.float32)
    speech = np.zeros(n, dtype=np.float32)
    ducked = dsp.duck(ambience, speech)
    np.testing.assert_allclose(ducked, ambience, atol=1e-4)


# ---------------------------------------------------------------- levels


def test_normalize_loudness_hits_target() -> None:
    rng = np.random.default_rng(9)
    x = (0.01 * rng.standard_normal(SR)).astype(np.float32)
    out = dsp.normalize_loudness(x, target_rms_db=-20.0)
    assert _rms_db(out) == pytest.approx(-20.0, abs=0.5)


def test_normalize_loudness_silence_passthrough() -> None:
    x = np.zeros(SR, dtype=np.float32)
    out = dsp.normalize_loudness(x)
    np.testing.assert_array_equal(out, x)


def test_peak_limit_respects_ceiling() -> None:
    t = np.arange(SR, dtype=np.float32) / SR
    x = (1.5 * np.sin(2 * np.pi * 100 * t)).astype(np.float32)
    out = dsp.peak_limit(x, ceiling=0.98)
    assert float(np.max(np.abs(out))) <= 0.98 + 1e-4


def test_peak_limit_no_change_below_ceiling() -> None:
    x = np.full(100, 0.5, dtype=np.float32)
    out = dsp.peak_limit(x)
    np.testing.assert_array_equal(out, x)


def test_mix_scene_output_valid() -> None:
    n = 2 * SR
    rng = np.random.default_rng(11)
    t = np.arange(n, dtype=np.float32) / SR
    speech = (0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    ambience = (0.1 * rng.standard_normal(n)).astype(np.float32)
    out = dsp.mix_scene(speech, ambience)
    assert len(out) == n
    assert float(np.max(np.abs(out))) <= 0.98 + 1e-4
    assert np.all(np.isfinite(out))


def test_mix_scene_pads_shorter_input() -> None:
    speech = np.zeros(SR, dtype=np.float32)
    ambience = np.full(2 * SR, 0.1, dtype=np.float32)
    out = dsp.mix_scene(speech, ambience)
    assert len(out) == 2 * SR


# ---------------------------------------------------------------- wav io


def test_wav_round_trip() -> None:
    rng = np.random.default_rng(13)
    # Keep within [-1, 1]: pipeline output is peak-limited before serialization.
    x = np.clip(0.3 * rng.standard_normal(SR), -0.95, 0.95).astype(np.float32)
    data = dsp.wav_bytes(x, SR)
    assert data[:4] == b"RIFF"
    y, sr = dsp.read_wav_bytes(data)
    assert sr == SR
    assert len(y) == len(x)
    np.testing.assert_allclose(y, x, atol=1e-3)  # PCM_16 quantization


def test_resample_linear() -> None:
    t = np.arange(48000, dtype=np.float32) / 48000
    x = np.sin(2 * np.pi * 5 * t).astype(np.float32)
    y = dsp.resample_linear(x, 48000, SR)
    assert len(y) == SR
    t24 = np.arange(SR, dtype=np.float32) / SR
    np.testing.assert_allclose(y, np.sin(2 * np.pi * 5 * t24), atol=1e-3)
