"""Tests for render_scene_audio using fake providers (no network)."""

import numpy as np

from app.adapters.base import TTSResult, Voice
from app.adapters.fake import FakeTTS
from app.ingest.elements import AttributedLine, NormalizedScene
from app.render.audio import dsp
from app.render.audio.model import SpeechClip
from app.render.audio.pipeline import (
    _TAIL_MS,
    render_scene_audio,
    render_scene_audio_with_timing,
)
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


async def test_with_timing_matches_audio_and_exposes_placement() -> None:
    scene = _scene()
    # Same seed/provider: the timing sibling must produce byte-identical audio
    # to the plain renderer (it is the same code path) and expose per-clip onsets.
    result_only = await render_scene_audio(scene, VOICE_MAP, FakeTTS(), seed=7)
    result, timing = await render_scene_audio_with_timing(
        scene, VOICE_MAP, FakeTTS(), seed=7
    )
    assert result.wav_bytes == result_only.wav_bytes
    assert timing.duration_ms == result.duration_ms
    assert timing.scene_ordinal == scene.ordinal
    assert timing.ambience_tags == result.ambience_tags

    # Placement must match the independently planned speech bus (ordinals 1,2,4,5,6;
    # parenthetical + transition dropped), start_ms and duration_ms per clip.
    ordinals = [1, 2, 4, 5, 6]
    block_ids = [0, 1, 2, 2, 3]
    durations = [len(t.split()) * 60 for t in [
        "Tom opens the door.",
        "You should not be out.",
        "The ship is off the shoals.",
        "Then it burns.",
        "Thunder outside.",
    ]]
    speakers = [None, "TOM", "MARA", "MARA", None]
    clips = [
        SpeechClip(
            line_ordinal=o, character_name=sp, duration_ms=d,
            beat_index=0, scene_ordinal=2, block_id=b,
        )
        for o, sp, d, b in zip(ordinals, speakers, durations, block_ids, strict=True)
    ]
    plan = plan_speech_bus(clips)
    assert [c.line_ordinal for c in timing.clips] == ordinals
    assert [c.start_ms for c in timing.clips] == [e.start_ms for e in plan.entries]
    assert [c.duration_ms for c in timing.clips] == durations
    # Kinds and speakers carried through (action lines have a None character).
    assert [c.kind for c in timing.clips] == [
        "action", "dialogue", "dialogue", "dialogue", "action"
    ]
    assert [c.character_name for c in timing.clips] == speakers


async def test_with_timing_sfx_markers_have_onsets() -> None:
    scene = NormalizedScene(
        ordinal=1, slugline="INT. ROOM - NIGHT", interior=True, location="ROOM",
        time_of_day="NIGHT",
        lines=[
            AttributedLine(ordinal=1, kind="action", text="A door slams shut."),
        ],
    )
    _result, timing = await render_scene_audio_with_timing(
        scene, {None: "n"}, FakeTTS(), seed=5
    )
    # The single action line's SFX is placed at that clip's onset (0).
    assert [(m.name, m.at_ms) for m in timing.sfx] == [("door_slam", 0)]


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


class TestRetryBudgetOutlastsAPerMinuteQuota:
    """A quota that resets per minute needs a retry window measured in minutes.

    Rendering a real scene returned `Quota exceeded for
    ...global_generate_content_requests_per_minute_per_project_per_base_model`.
    The budget then was three attempts from a 0.5s base — it gave up about 1.5
    seconds into a limit that clears after sixty, so every retry landed inside
    the same exhausted window and the render surfaced a 502.
    """

    @staticmethod
    def _worst_case_wait(attempts: int, base: float) -> float:
        """Total backoff across all retries, matching run_with_retries."""
        return sum(base * (2**i) for i in range(attempts - 1))

    def test_default_budget_spans_at_least_a_minute(self) -> None:
        from app.render.audio import pipeline

        waited = self._worst_case_wait(
            pipeline._TTS_MAX_ATTEMPTS, pipeline._TTS_BASE_DELAY_S
        )
        assert waited >= 60.0, (
            f"retry window is {waited:.1f}s; a per-minute quota needs at least 60s"
        )

    def test_budget_is_tunable(self, monkeypatch) -> None:
        """The right values depend on the provider's quota, not on this file."""
        import importlib

        from app.render.audio import pipeline

        monkeypatch.setenv("STORY_ENGINE_TTS_CONCURRENCY", "1")
        monkeypatch.setenv("STORY_ENGINE_TTS_MAX_ATTEMPTS", "9")
        monkeypatch.setenv("STORY_ENGINE_TTS_BASE_DELAY_S", "3.5")
        reloaded = importlib.reload(pipeline)
        try:
            assert reloaded._CONCURRENCY == 1
            assert reloaded._TTS_MAX_ATTEMPTS == 9
            assert reloaded._TTS_BASE_DELAY_S == 3.5
        finally:
            monkeypatch.undo()
            importlib.reload(pipeline)

    def test_unusable_values_fall_back_to_the_default(self, monkeypatch) -> None:
        import importlib

        from app.render.audio import pipeline

        monkeypatch.setenv("STORY_ENGINE_TTS_CONCURRENCY", "not-a-number")
        monkeypatch.setenv("STORY_ENGINE_TTS_MAX_ATTEMPTS", "0")
        reloaded = importlib.reload(pipeline)
        try:
            assert reloaded._CONCURRENCY == 4, "garbage should not disable concurrency"
            assert reloaded._TTS_MAX_ATTEMPTS >= 1, "zero attempts renders nothing"
        finally:
            monkeypatch.undo()
            importlib.reload(pipeline)


class TestTimelineReportsWhatWasSynthesized:
    """The timeline's emotion must be the one the clip was rendered with.

    Reporting the line's own emotion instead showed null for a clip the casting
    delivered as 'calm' - a timeline that disagrees with its own audio. The
    casting reached the synthesizer correctly; only the report was wrong, which
    is the harder version of the bug to notice.
    """

    @staticmethod
    def _scene(fountain: str):
        from app.ingest.fountain import parse_fountain
        from app.ingest.normalize import normalize

        return normalize(parse_fountain(fountain)).scenes[1]

    async def test_casting_tone_appears_in_the_timeline(self, sample_fountain: str):
        from app.adapters.fake import FakeTTS
        from app.render.audio.pipeline import render_scene_audio_with_timing

        scene = self._scene(sample_fountain)
        tts = FakeTTS()
        voices = await tts.list_voices()
        speakers = {
            line.character_name
            for line in scene.lines
            if line.kind == "dialogue" and line.character_name
        }
        voice_map = {None: voices[0].id}
        for name in speakers:
            voice_map[name] = voices[-1].id

        _result, timing = await render_scene_audio_with_timing(
            scene, voice_map, tts, tone_map={None: "calm"}
        )

        narration = [c for c in timing.clips if c.character_name is None]
        assert narration, "expected narration clips"
        assert all(clip.emotion == "calm" for clip in narration)

    async def test_a_lines_own_emotion_still_wins(self, sample_fountain: str):
        """A parenthetical is specific; a casting is general. Specific wins."""
        from app.adapters.fake import FakeTTS
        from app.render.audio.pipeline import render_scene_audio_with_timing

        scene = self._scene(sample_fountain)
        tts = FakeTTS()
        voices = await tts.list_voices()
        tagged = [line for line in scene.lines if line.emotion]
        if not tagged:
            import pytest

            pytest.skip("fixture has no parenthetical-tagged line")

        voice_map = {None: voices[0].id}
        for line in scene.lines:
            if line.kind == "dialogue" and line.character_name:
                voice_map[line.character_name] = voices[-1].id

        _result, timing = await render_scene_audio_with_timing(
            scene,
            voice_map,
            tts,
            tone_map=dict.fromkeys(voice_map, "shouting"),
        )

        by_ordinal = {clip.line_ordinal: clip for clip in timing.clips}
        for line in tagged:
            clip = by_ordinal.get(line.ordinal)
            if clip is not None:
                assert clip.emotion == line.emotion
