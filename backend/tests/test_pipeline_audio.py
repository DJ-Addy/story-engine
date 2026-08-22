"""Tests for render_scene_audio using fake providers (no network)."""

import numpy as np

from app.adapters.base import TTSResult, Voice
from app.adapters.fake import FakeTTS
from app.ingest.elements import AttributedLine, NormalizedScene
from app.render.audio import dsp
from app.render.audio.model import SpeechClip
from app.render.audio.pipeline import _TAIL_MS, render_scene_audio
from app.render.audio.timing import plan_speech_bus

VOICE_MAP: dict[str | None, str] = {
    None: "narrator-voice",
    "MARA": "mara-voice",
    "TOM": "tom-voice",
}


def _scene() -> NormalizedScene:
    return NormalizedScene(
        ordinal=2,
        slugline="INT. LIGHTHOUSE - KEEPER'S ROOM - NIGHT",
        interior=True,
        location="LIGHTHOUSE - KEEPER'S ROOM",
        time_of_day="NIGHT",
        lines=[
            AttributedLine(ordinal=1, kind="action", text="Tom opens the door."),
            AttributedLine(
                ordinal=2, kind="dialogue", text="You should not be out.", character_name="TOM"
            ),
            AttributedLine(ordinal=3, kind="parenthetical", text="(beat)", character_name="TOM"),
            AttributedLine(
                ordinal=4, kind="dialogue", text="The ship is off the shoals.",
                character_name="MARA",
            ),
            AttributedLine(ordinal=5, kind="dialogue", text="Then it burns.", character_name="MARA"),
            AttributedLine(ordinal=6, kind="action", text="Thunder outside."),
            AttributedLine(ordinal=7, kind="transition", text="CUT TO:"),
        ],
    )


async def test_render_scene_with_fake_tts() -> None:
    scene = _scene()
    result = await render_scene_audio(scene, VOICE_MAP, FakeTTS(), seed=7)

    # Parenthetical and transition are skipped; 2 action + 3 dialogue remain.
    assert result.clip_count == 5

    # Interior night keeper's room + thunder in action text.
    assert "room_tone" in result.ambience_tags
    assert "rain" in result.ambience_tags
    assert "thunder_distant" in result.ambience_tags

    # Duration math: FakeTTS reports words*60 ms per line; blocks follow
    # speaker/role changes, so expected timeline is reproducible here.
    spoken = [
        ("Tom opens the door.", None),
        ("You should not be out.", "TOM"),
        ("The ship is off the shoals.", "MARA"),
        ("Then it burns.", "MARA"),
        ("Thunder outside.", None),
    ]
    ordinals = [1, 2, 4, 5, 6]
    block_ids = [0, 1, 2, 2, 3]
    clips = [
        SpeechClip(
            line_ordinal=ordinal,
            character_name=speaker,
            duration_ms=len(text.split()) * 60,
            beat_index=0,
            scene_ordinal=2,
            block_id=block,
        )
        for (text, speaker), ordinal, block in zip(spoken, ordinals, block_ids, strict=True)
    ]
    expected_total_ms = plan_speech_bus(clips).total_ms + _TAIL_MS
    assert result.duration_ms == expected_total_ms

    # Output must be a valid mono 24 kHz WAV of exactly that duration.
    samples, sr = dsp.read_wav_bytes(result.wav_bytes)
    assert sr == dsp.SR
    assert len(samples) == round(expected_total_ms * dsp.SR / 1000)
    assert float(np.max(np.abs(samples))) <= 0.98 + 1e-4
    assert np.any(samples != 0.0)


async def test_render_scene_deterministic_ambience() -> None:
    scene = _scene()
    a = await render_scene_audio(scene, VOICE_MAP, FakeTTS(), seed=7)
    b = await render_scene_audio(scene, VOICE_MAP, FakeTTS(), seed=7)
    assert a.wav_bytes == b.wav_bytes


async def test_render_scene_survives_retryable_failures() -> None:
    scene = _scene()
    result = await render_scene_audio(scene, VOICE_MAP, FakeTTS(fail_times=1), seed=7)
    assert result.clip_count == 5


class _WavTTS:
    """TTS stub returning genuine WAV audio at a non-native sample rate."""

    name = "wav-stub"

    async def synthesize(
        self, text: str, voice_id: str, emotion: str | None, params: dict
    ) -> TTSResult:
        sr = 48000
        t = np.arange(sr // 2, dtype=np.float32) / sr  # exactly 500 ms
        tone = (0.3 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
        return TTSResult(
            audio_bytes=dsp.wav_bytes(tone, sr),
            duration_ms=500,
            cost_cents=0,
            provider=self.name,
            model=voice_id,
            gen_params={},
        )

    async def list_voices(self) -> list[Voice]:
        return []

    def estimate_cost_cents(self, text: str) -> int:
        return 0


async def test_real_wav_clips_are_used_and_resampled() -> None:
    scene = NormalizedScene(
        ordinal=1,
        slugline=None,
        interior=True,
        location=None,
        time_of_day=None,
        lines=[AttributedLine(ordinal=1, kind="action", text="A single narrated line.")],
    )
    result = await render_scene_audio(scene, {None: "v"}, _WavTTS(), seed=1)
    assert result.clip_count == 1
    # One 500 ms clip starting at 0 plus the ambience tail.
    assert result.duration_ms == 500 + _TAIL_MS


class _EmotionCapturingTTS:
    """TTS stub that records the (text, emotion) it was called with and
    returns real WAV bytes so the clip is actually used by the mixer."""

    name = "emotion-capture"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def synthesize(
        self, text: str, voice_id: str, emotion: str | None, params: dict
    ) -> TTSResult:
        self.calls.append((text, emotion))
        sr = 24000
        t = np.arange(sr // 4, dtype=np.float32) / sr  # 250 ms
        tone = (0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
        return TTSResult(
            audio_bytes=dsp.wav_bytes(tone, sr),
            duration_ms=250,
            cost_cents=0,
            provider=self.name,
            model=voice_id,
            gen_params={"emotion": emotion},
        )

    async def list_voices(self) -> list[Voice]:
        return []

    def estimate_cost_cents(self, text: str) -> int:
        return 0


async def test_line_emotion_reaches_tts_provider() -> None:
    scene = NormalizedScene(
        ordinal=1,
        slugline="INT. OFFICE - DAY",
        interior=True,
        location="OFFICE",
        time_of_day="DAY",
        lines=[
            AttributedLine(
                ordinal=1,
                kind="dialogue",
                text="Get out now.",
                character_name="MARA",
                emotion="angry",
            ),
            AttributedLine(
                ordinal=2,
                kind="dialogue",
                text="Fine.",
                character_name="TOM",
                emotion=None,
            ),
        ],
    )
    tts = _EmotionCapturingTTS()
    result = await render_scene_audio(scene, VOICE_MAP, tts, seed=3)

    assert result.clip_count == 2
    assert set(tts.calls) == {("Get out now.", "angry"), ("Fine.", None)}
