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
from app.render.audio import dsp
from app.render.audio.model import SpeechClip
from app.render.audio.timing import plan_speech_bus

_CONCURRENCY = 4
_AMBIENCE_BASE_S = 20.0  # synth this much bed, then loop to scene length
_TAIL_MS = 1200  # let the ambience breathe after the last line

_SPOKEN_KINDS = {"dialogue", "action", "narration"}


class SceneRenderResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    wav_bytes: bytes
    duration_ms: int
    clip_count: int
    ambience_tags: list[str]


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
) -> SceneRenderResult:
    lines = [line for line in scene.lines if line.kind in _SPOKEN_KINDS and line.text.strip()]

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

    clips: list[SpeechClip] = []
    block_id = -1
    prev_speaker: str | None = None
    prev_kind: str | None = None
    for line, samples in zip(lines, sample_arrays, strict=True):
        speaker = line.character_name if line.kind == "dialogue" else None
        # New block whenever the speaker or narration/dialogue role changes.
        if block_id < 0 or speaker != prev_speaker or line.kind != prev_kind:
            block_id += 1
        prev_speaker, prev_kind = speaker, line.kind
        clips.append(
            SpeechClip(
                line_ordinal=line.ordinal,
                character_name=speaker,
                duration_ms=round(len(samples) / dsp.SR * 1000),
                beat_index=0,
                scene_ordinal=scene.ordinal,
                block_id=block_id,
            )
        )

    plan = plan_speech_bus(clips)
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

    mixed = dsp.mix_scene(speech_bus, bed)
    return SceneRenderResult(
        wav_bytes=dsp.wav_bytes(mixed, dsp.SR),
        duration_ms=round(len(mixed) / dsp.SR * 1000),
        clip_count=len(clips),
        ambience_tags=tags,
    )
