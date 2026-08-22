"""Tests for the provider adapter layer: protocols, fakes, and the registry."""

import pytest

from app.adapters.base import (
    ImageProvider,
    ImageResult,
    LLMProvider,
    LLMResult,
    RetryableProviderError,
    TerminalProviderError,
    TTSProvider,
    TTSResult,
    Voice,
    classify_http_status,
)
from app.adapters.fake import FakeImage, FakeLLM, FakeTTS
from app.adapters.registry import ProviderRegistry


class TestProtocolConformance:
    def test_fake_tts_is_tts_provider(self) -> None:
        assert isinstance(FakeTTS(), TTSProvider)

    def test_fake_image_is_image_provider(self) -> None:
        assert isinstance(FakeImage(), ImageProvider)

    def test_fake_llm_is_llm_provider(self) -> None:
        assert isinstance(FakeLLM(), LLMProvider)


class TestFakeTTS:
    async def test_synthesize_returns_tts_result(self) -> None:
        tts = FakeTTS()
        result = await tts.synthesize("hello world", voice_id="v1", emotion=None, params={})
        assert isinstance(result, TTSResult)
        assert result.provider == tts.name
        assert isinstance(result.audio_bytes, bytes)
        assert len(result.audio_bytes) > 0

    async def test_duration_is_60ms_per_word(self) -> None:
        tts = FakeTTS()
        result = await tts.synthesize("one two three", voice_id="v1", emotion=None, params={})
        assert result.duration_ms == 3 * 60

    async def test_cost_is_1_cent_per_100_chars(self) -> None:
        tts = FakeTTS()
        text = "x" * 250
        result = await tts.synthesize(text, voice_id="v1", emotion=None, params={})
        assert result.cost_cents == tts.estimate_cost_cents(text)
        assert result.cost_cents == 3  # ceil(250 / 100)

    async def test_determinism_same_input_same_output(self) -> None:
        a = await FakeTTS().synthesize("the same text", voice_id="v1", emotion="calm", params={})
        b = await FakeTTS().synthesize("the same text", voice_id="v1", emotion="calm", params={})
        assert a.audio_bytes == b.audio_bytes
        assert a.duration_ms == b.duration_ms
        assert a.cost_cents == b.cost_cents

    async def test_list_voices_returns_voices(self) -> None:
        voices = await FakeTTS().list_voices()
        assert len(voices) > 0
        assert all(isinstance(v, Voice) for v in voices)


class TestFakeImage:
    async def test_generate_returns_image_result(self) -> None:
        img = FakeImage()
        result = await img.generate("a castle", seed=42, params={})
        assert isinstance(result, ImageResult)
        assert result.provider == img.name
        assert result.seed == 42
        assert result.cost_cents == 4

    async def test_generate_deterministic_for_same_prompt_and_seed(self) -> None:
        a = await FakeImage().generate("a castle", seed=7, params={})
        b = await FakeImage().generate("a castle", seed=7, params={})
        assert a.image_bytes == b.image_bytes

    async def test_generate_differs_across_seeds(self) -> None:
        a = await FakeImage().generate("a castle", seed=1, params={})
        b = await FakeImage().generate("a castle", seed=2, params={})
        assert a.image_bytes != b.image_bytes

    async def test_generate_from_refs(self) -> None:
        img = FakeImage()
        result = await img.generate_from_refs("a castle", refs=[b"ref1"], seed=3, params={})
        assert isinstance(result, ImageResult)
        assert result.seed == 3

    async def test_generate_from_refs_respects_max_reference_images(self) -> None:
        img = FakeImage()
        too_many = [b"r"] * (img.max_reference_images + 1)
        with pytest.raises(ValueError):
            await img.generate_from_refs("a castle", refs=too_many, seed=1, params={})

    def test_estimate_cost_cents(self) -> None:
        assert FakeImage().estimate_cost_cents(3) == 12


class TestFakeLLM:
    async def test_complete_returns_canned_response(self) -> None:
        llm = FakeLLM(response="canned")
        result = await llm.complete(system="sys", user="usr", params={})
        assert isinstance(result, LLMResult)
        assert result.text == "canned"
        assert result.provider == llm.name

    async def test_cost_scales_with_prompt_chars(self) -> None:
        llm = FakeLLM()
        result = await llm.complete(system="a" * 100, user="b" * 100, params={})
        assert result.cost_cents == llm.estimate_cost_cents(200)


class TestFailureInjection:
    async def test_tts_fail_times_raises_retryable_then_succeeds(self) -> None:
        tts = FakeTTS(fail_times=2)
        with pytest.raises(RetryableProviderError):
            await tts.synthesize("t", voice_id="v", emotion=None, params={})
        with pytest.raises(RetryableProviderError):
            await tts.synthesize("t", voice_id="v", emotion=None, params={})
        result = await tts.synthesize("t", voice_id="v", emotion=None, params={})
        assert isinstance(result, TTSResult)

    async def test_tts_terminal_fail(self) -> None:
        tts = FakeTTS(terminal_fail=True)
        with pytest.raises(TerminalProviderError):
            await tts.synthesize("t", voice_id="v", emotion=None, params={})

    async def test_image_failure_injection(self) -> None:
        img = FakeImage(fail_times=1)
        with pytest.raises(RetryableProviderError):
            await img.generate("p", seed=1, params={})
        result = await img.generate("p", seed=1, params={})
        assert isinstance(result, ImageResult)

    async def test_llm_failure_injection(self) -> None:
        llm = FakeLLM(terminal_fail=True)
        with pytest.raises(TerminalProviderError):
            await llm.complete(system="s", user="u", params={})


class TestClassifyHttpStatus:
    @pytest.mark.parametrize("code", [429, 500, 502, 503, 504, 599])
    def test_retryable_codes(self, code: int) -> None:
        assert classify_http_status(code) == "retryable"

    @pytest.mark.parametrize("code", [400, 403, 422])
    def test_terminal_codes(self, code: int) -> None:
        assert classify_http_status(code) == "terminal"


class TestProviderRegistry:
    def test_resolve_returns_first_registered(self) -> None:
        reg = ProviderRegistry()
        first = FakeTTS()
        second = FakeTTS()
        reg.register("tts", first)
        reg.register("tts", second)
        assert reg.resolve("tts") is first

    def test_resolve_all_preserves_registration_order(self) -> None:
        reg = ProviderRegistry()
        providers = [FakeLLM(), FakeLLM(), FakeLLM()]
        for p in providers:
            reg.register("llm", p)
        assert reg.resolve_all("llm") == providers

    def test_capabilities_are_independent(self) -> None:
        reg = ProviderRegistry()
        tts = FakeTTS()
        img = FakeImage()
        reg.register("tts", tts)
        reg.register("image", img)
        assert reg.resolve("tts") is tts
        assert reg.resolve("image") is img

    def test_resolve_unknown_capability_raises(self) -> None:
        reg = ProviderRegistry()
        with pytest.raises(KeyError):
            reg.resolve("tts")

    def test_resolve_all_unknown_capability_returns_empty(self) -> None:
        assert ProviderRegistry().resolve_all("image") == []
