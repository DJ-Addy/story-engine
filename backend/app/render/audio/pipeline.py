"""End-to-end scene audio renderer: TTS -> speech-bus plan -> ambience -> mix.

Executes the planning layer (plan_speech_bus) with real samples via the numpy
DSP engine. Provider-agnostic: anything satisfying TTSProvider works. Results
whose audio_bytes are not decodable WAV (e.g. FakeTTS hash bytes) get a
placeholder tone of the reported duration so tests stay meaningful.
"""

from __future__ import annotations

import asyncio

import numpy as np
from pydantic import BaseModel, ConfigDict

from app.adapters.base import TTSProvider, TTSResult
from app.costs.retry import run_with_retries
from app.ingest.elements import NormalizedScene
from app.nlp.ambience import ambience_tags
from app.nlp.sound_events import detect_sound_events
from app.render.audio import dsp, sfx
from app.render.audio.model import (
    DEFAULT_RENDER_SETTINGS,
    SPEECH_TAIL_MS,
    RenderedClip,
    RenderedSfx,
    SceneRenderSettings,
    SceneTiming,
)
from app.render.audio.timing import plan_speech_bus, speech_clips, spoken_lines

_CONCURRENCY = 4
_AMBIENCE_BASE_S = 20.0  # synth this much bed, then loop to scene length
_TAIL_MS = SPEECH_TAIL_MS  # let the ambience breathe after the last line

# Lines whose description can trigger a foreground sound event.
_EVENT_KINDS = {"action", "narration"}
_SFX_GAIN = 0.6  # foreground SFX level before the final loudness/limit stage


class SceneRenderResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    wav_bytes: bytes
    duration_ms: int
    clip_count: int
    ambience_tags: list[str]
    sfx_events: list[str] = []  # foreground SFX placed, in timeline order


def _build_sfx_bus(
    lines: list,
    plan,
    total_samples: int,
    seed: int,
    scene_ordinal: int,
) -> tuple[np.ndarray, list[RenderedSfx]]:
    """Place synthesized SFX for each action line at its narration time.

    Events from one line are laid down back-to-back starting at that line's
    clip onset, so the sound tracks the words describing it. Returns the SFX
    bus and the ordered list of placed events with their onset times (for
    reporting/tests and the scene timeline).
    """
    bus = np.zeros(total_samples, dtype=np.float32)
    placed: list[RenderedSfx] = []
    rng = np.random.default_rng([seed, 0x5F_C0DE, scene_ordinal])
    _gap = round(0.15 * dsp.SR)
    for entry in plan.entries:
        line = lines[entry.clip_index]
        if line.kind not in _EVENT_KINDS:
            continue
        cursor = round(entry.start_ms * dsp.SR / 1000)
        for event in detect_sound_events(line.text):
            clip = _SFX_GAIN * sfx.synth_event(event, rng)
            if cursor >= total_samples:
                break
            end = min(cursor + len(clip), total_samples)
            bus[cursor:end] += clip[: end - cursor]
            placed.append(RenderedSfx(at_ms=round(cursor * 1000 / dsp.SR), name=event))
            cursor = end + _gap
    return bus, placed


def _clip_samples(result: TTSResult) -> np.ndarray:
    """Decode a TTSResult to samples at dsp.SR, tolerating non-WAV payloads."""
    try:
        samples, sr = dsp.read_wav_bytes(result.audio_bytes)
        return dsp.resample_linear(samples, sr, dsp.SR)
    except Exception:
        # Placeholder tone for fake/undecodable providers, at reported duration.
        n = round(result.duration_ms * dsp.SR / 1000)
        t = np.arange(n, dtype=np.float32) / dsp.SR
        return (0.2 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


async def render_scene_audio(
    scene: NormalizedScene,
    voice_map: dict[str | None, str],
    tts: TTSProvider,
    seed: int = 7,
    settings: SceneRenderSettings | None = None,
) -> SceneRenderResult:
    """Render a scene to a mixed WAV. Backward-compatible thin wrapper around
    :func:`render_scene_audio_with_timing` that discards the timing payload; the
    audio bytes are byte-identical to that function's (same code path)."""
    result, _timing = await render_scene_audio_with_timing(
        scene, voice_map, tts, seed, settings
    )
    return result


async def render_scene_audio_with_timing(
    scene: NormalizedScene,
    voice_map: dict[str | None, str],
    tts: TTSProvider,
    seed: int = 7,
    settings: SceneRenderSettings | None = None,
) -> tuple[SceneRenderResult, SceneTiming]:
    """Render a scene AND expose the per-clip placement used to build it.

    Identical audio to :func:`render_scene_audio` — the timing is read off the
    very same speech-bus plan and SFX pass that produce the WAV, so every onset
    lines up with the rendered bytes to the millisecond.

    ``settings`` carries the timeline editor's per-scene knobs (pacing, ambience
    duck). Omitting it, or passing defaults, renders exactly as before.
    """
    settings = settings or DEFAULT_RENDER_SETTINGS
    lines = spoken_lines(scene)

    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def synthesize(text: str, voice_id: str, emotion: str | None) -> TTSResult:
        async with semaphore:
            return await run_with_retries(
                lambda: tts.synthesize(text, voice_id, emotion, {}), base_delay_s=0.5
            )

    tasks = []
    for line in lines:
        speaker = line.character_name if line.kind == "dialogue" else None
        voice_id = voice_map.get(speaker, voice_map[None])
        tasks.append(synthesize(line.text, voice_id, line.emotion))
    results = await asyncio.gather(*tasks)

    sample_arrays = [_clip_samples(result) for result in results]

    # Measured durations, then the shared block-grouping rule the estimator
    # also uses (app.render.audio.timing.speech_clips).
    clips = speech_clips(
        lines,
        [round(len(samples) / dsp.SR * 1000) for samples in sample_arrays],
        scene.ordinal,
    )

    plan = plan_speech_bus(clips, gap_scale=settings.pacing)
    total_ms = plan.total_ms + _TAIL_MS
    speech_bus = dsp.place_clips(
        [
            (sample_arrays[entry.clip_index], entry.start_ms)
            for entry in plan.entries
        ],
        total_ms,
    )

    action_text = " ".join(line.text for line in scene.lines if line.kind == "action")
    tags = ambience_tags(
        scene.location, scene.time_of_day, scene.interior, weather=None, action_text=action_text
    )
    total_samples = round(total_ms * dsp.SR / 1000)
    bed = dsp.synth_ambience(tags, min(total_ms / 1000.0, _AMBIENCE_BASE_S), seed)
    bed = dsp.loop_to_length(bed, total_samples)

    sfx_bus, sfx_markers = _build_sfx_bus(lines, plan, total_samples, seed, scene.ordinal)

    mixed = dsp.mix_scene(speech_bus, bed, sfx_bus, duck_ratio=settings.duck_ratio)
    duration_ms = round(len(mixed) / dsp.SR * 1000)

    rendered_clips = [
        RenderedClip(
            line_ordinal=lines[entry.clip_index].ordinal,
            kind=lines[entry.clip_index].kind,
            character_name=clips[entry.clip_index].character_name,
            emotion=lines[entry.clip_index].emotion,
            text=lines[entry.clip_index].text,
            start_ms=entry.start_ms,
            duration_ms=clips[entry.clip_index].duration_ms,
        )
        for entry in plan.entries
    ]
    timing = SceneTiming(
        scene_ordinal=scene.ordinal,
        duration_ms=duration_ms,
        clips=rendered_clips,
        sfx=sfx_markers,
        ambience_tags=tags,
    )

    result = SceneRenderResult(
        wav_bytes=dsp.wav_bytes(mixed, dsp.SR),
        duration_ms=duration_ms,
        clip_count=len(clips),
        ambience_tags=tags,
        sfx_events=[marker.name for marker in sfx_markers],
    )
    return result, timing
