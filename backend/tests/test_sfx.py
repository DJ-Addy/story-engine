"""Tests for one-shot SFX synthesis and its use in the render pipeline."""

import numpy as np
import pytest

from app.adapters.base import TTSResult, Voice
from app.ingest.elements import AttributedLine, NormalizedScene
from app.render.audio import dsp, sfx


@pytest.mark.parametrize("event", sorted(sfx.EVENTS))
def test_event_is_finite_bounded_mono(event: str) -> None:
    clip = sfx.synth_event(event, np.random.default_rng(3))
    assert clip.dtype == np.float32
    assert clip.ndim == 1 and len(clip) > 0
    assert np.all(np.isfinite(clip))
    assert float(np.max(np.abs(clip))) <= 1.0 + 1e-6


@pytest.mark.parametrize("event", sorted(sfx.EVENTS))
def test_event_is_deterministic(event: str) -> None:
    a = sfx.synth_event(event, np.random.default_rng(11))
    b = sfx.synth_event(event, np.random.default_rng(11))
    assert np.array_equal(a, b)


def test_unknown_event_raises() -> None:
    with pytest.raises(KeyError):
        sfx.synth_event("laser_blast", np.random.default_rng(0))


def test_mix_scene_with_sfx_stays_under_ceiling() -> None:
    rng = np.random.default_rng(0)
    speech = 0.4 * rng.standard_normal(dsp.SR).astype(np.float32)
    bed = 0.2 * rng.standard_normal(dsp.SR).astype(np.float32)
    sfx_bus = np.zeros(dsp.SR, dtype=np.float32)
    sfx_bus[: len(clip := sfx.thunder(rng))] += clip[: dsp.SR]
    mixed = dsp.mix_scene(speech, bed, sfx_bus)
    assert float(np.max(np.abs(mixed))) <= 0.98 + 1e-4


class _WavTTS:
    """Returns real 200 ms WAV audio so clips are placed on the timeline."""

    name = "wav-stub"

    async def synthesize(self, text: str, voice_id: str, emotion: str | None, params: dict) -> TTSResult:
        t = np.arange(dsp.SR // 5, dtype=np.float32) / dsp.SR
        tone = (0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
        return TTSResult(
            audio_bytes=dsp.wav_bytes(tone, dsp.SR),
            duration_ms=200, cost_cents=0, provider=self.name, model=voice_id, gen_params={},
        )

    async def list_voices(self) -> list[Voice]:
        return []

    def estimate_cost_cents(self, text: str) -> int:
        return 0


async def test_pipeline_places_action_triggered_sfx() -> None:
    from app.render.audio.pipeline import render_scene_audio

    scene = NormalizedScene(
        ordinal=1, slugline="INT. ROOM - NIGHT", interior=True, location="ROOM", time_of_day="NIGHT",
        lines=[
            AttributedLine(ordinal=1, kind="action", text="Tom opens the door. Wind bursts in."),
            AttributedLine(ordinal=2, kind="dialogue", text="Get inside.", character_name="TOM"),
            AttributedLine(ordinal=3, kind="action", text="Outside, thunder."),
        ],
    )
    result = await render_scene_audio(scene, {None: "n", "TOM": "t"}, _WavTTS(), seed=7)
    assert result.sfx_events == ["door_open", "wind_gust", "thunder"]
    # SFX must not extend the scene past its speech-plan duration.
    samples, _ = dsp.read_wav_bytes(result.wav_bytes)
    assert len(samples) == round(result.duration_ms * dsp.SR / 1000)


async def test_pipeline_deterministic_with_sfx() -> None:
    from app.render.audio.pipeline import render_scene_audio

    scene = NormalizedScene(
        ordinal=1, slugline=None, interior=True, location=None, time_of_day=None,
        lines=[AttributedLine(ordinal=1, kind="action", text="A door slams shut.")],
    )
    a = await render_scene_audio(scene, {None: "n"}, _WavTTS(), seed=5)
    b = await render_scene_audio(scene, {None: "n"}, _WavTTS(), seed=5)
    assert a.sfx_events == ["door_slam"] and a.wav_bytes == b.wav_bytes
