"""Deterministic fake providers for tests. Never spend credits, never touch
the network. Support failure injection for retry-path testing."""

import hashlib
import math

from app.adapters.base import (
    ImageResult,
    LLMResult,
    RetryableProviderError,
    TerminalProviderError,
    TTSResult,
    VideoResult,
    Voice,
)


class _FailureInjector:
    """Shared failure-injection behavior for the fakes."""

    def __init__(self, fail_times: int = 0, terminal_fail: bool = False) -> None:
        self._remaining_failures = fail_times
        self._terminal_fail = terminal_fail

    def _maybe_fail(self) -> None:
        if self._terminal_fail:
            raise TerminalProviderError("injected terminal failure")
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise RetryableProviderError("injected retryable failure")


def _deterministic_bytes(*parts: str) -> bytes:
    return hashlib.sha256("|".join(parts).encode("utf-8")).digest()


class FakeTTS(_FailureInjector):
    name = "fake-tts"
    model = "fake-tts-v1"

    def __init__(self, fail_times: int = 0, terminal_fail: bool = False) -> None:
        super().__init__(fail_times=fail_times, terminal_fail=terminal_fail)

    async def synthesize(
        self, text: str, voice_id: str, emotion: str | None, params: dict
    ) -> TTSResult:
        self._maybe_fail()
        return TTSResult(
            audio_bytes=_deterministic_bytes("tts", text, voice_id, emotion or ""),
            duration_ms=len(text.split()) * 60,
            cost_cents=self.estimate_cost_cents(text),
            provider=self.name,
            model=self.model,
            gen_params={"voice_id": voice_id, "emotion": emotion, **params},
        )

    async def list_voices(self) -> list[Voice]:
        return [
            Voice(id="fake-narrator", name="Fake Narrator", tags=["neutral", "narration"]),
            Voice(id="fake-hero", name="Fake Hero", tags=["young", "energetic"]),
        ]

    def estimate_cost_cents(self, text: str) -> int:
        return math.ceil(len(text) / 100)


class FakeImage(_FailureInjector):
    name = "fake-image"
    model = "fake-image-v1"
    max_reference_images = 3

    COST_CENTS_PER_IMAGE = 4

    def __init__(self, fail_times: int = 0, terminal_fail: bool = False) -> None:
        super().__init__(fail_times=fail_times, terminal_fail=terminal_fail)

    async def generate(self, prompt: str, seed: int, params: dict) -> ImageResult:
        self._maybe_fail()
        return ImageResult(
            image_bytes=_deterministic_bytes("image", prompt, str(seed)),
            cost_cents=self.COST_CENTS_PER_IMAGE,
            provider=self.name,
            model=self.model,
            seed=seed,
            gen_params=dict(params),
        )

    async def generate_from_refs(
        self, prompt: str, refs: list[bytes], seed: int, params: dict
    ) -> ImageResult:
        if len(refs) > self.max_reference_images:
            raise ValueError(
                f"{len(refs)} reference images exceeds max of {self.max_reference_images}"
            )
        self._maybe_fail()
        ref_digest = hashlib.sha256(b"".join(refs)).hexdigest()
        return ImageResult(
            image_bytes=_deterministic_bytes("image-refs", prompt, str(seed), ref_digest),
            cost_cents=self.COST_CENTS_PER_IMAGE,
            provider=self.name,
            model=self.model,
            seed=seed,
            gen_params=dict(params),
        )

    def estimate_cost_cents(self, n: int) -> int:
        return n * self.COST_CENTS_PER_IMAGE


class FakeVideo(_FailureInjector):
    """Deterministic image/text -> video provider for tests. Never touches the
    network. Records each ``generate`` call so tests can assert which path
    (image-to-video vs text-to-video) the endpoint took."""

    name = "fake-video"
    model = "fake-video-v1"

    CENTS_PER_SECOND = 5

    def __init__(self, fail_times: int = 0, terminal_fail: bool = False) -> None:
        super().__init__(fail_times=fail_times, terminal_fail=terminal_fail)
        self.calls: list[dict] = []

    async def generate(
        self,
        prompt: str | None = None,
        *,
        image: bytes | str | None = None,
        duration_s: int = 5,
        model: str | None = None,
        params: dict | None = None,
    ) -> VideoResult:
        self._maybe_fail()
        has_image = image is not None
        self.calls.append(
            {"prompt": prompt, "has_image": has_image, "duration_s": duration_s}
        )
        model_id = model or self.model
        return VideoResult(
            video_bytes=_deterministic_bytes(
                "video", prompt or "", str(has_image), str(duration_s)
            ),
            output_urls=[
                f"https://fake.local/{_deterministic_bytes('video-url', prompt or '').hex()}.mp4"
            ],
            duration_ms=duration_s * 1000,
            cost_cents=self.estimate_cost_cents(duration_s, model_id),
            provider=self.name,
            model=model_id,
            gen_params={
                "has_image": has_image,
                "prompt": prompt,
                "duration_s": duration_s,
                **(params or {}),
            },
        )

    def estimate_cost_cents(self, duration_s: int, model: str | None = None) -> int:
        return max(1, duration_s * self.CENTS_PER_SECOND)


class FakeLLM(_FailureInjector):
    name = "fake-llm"
    model = "fake-llm-v1"

    def __init__(
        self,
        response: str = "This is a canned fake LLM response.",
        fail_times: int = 0,
        terminal_fail: bool = False,
    ) -> None:
        super().__init__(fail_times=fail_times, terminal_fail=terminal_fail)
        self._response = response

    async def complete(self, system: str, user: str, params: dict) -> LLMResult:
        self._maybe_fail()
        return LLMResult(
            text=self._response,
            cost_cents=self.estimate_cost_cents(len(system) + len(user)),
            provider=self.name,
            model=self.model,
        )

    def estimate_cost_cents(self, prompt_chars: int) -> int:
        return math.ceil(prompt_chars / 1000)
