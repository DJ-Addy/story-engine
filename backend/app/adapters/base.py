"""Provider adapter contracts: result models, protocols, and error taxonomy.

ARCHITECTURAL LAW (PRD): no provider SDK is imported outside app/adapters/.
Every adapter reports cost_cents on its results so the cost governor can
enforce project caps.
"""

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class ProviderError(Exception):
    """Base class for provider-side failures."""


class RetryableProviderError(ProviderError):
    """Transient failure (rate limit, server error) — safe to retry."""


class TerminalProviderError(ProviderError):
    """Permanent failure (bad request, content policy) — never retry."""


def classify_http_status(code: int) -> Literal["retryable", "terminal"]:
    """Map an HTTP status code to a retry classification.

    429 and all 5xx are retryable; everything else (400/403/422,
    content-policy rejections, etc.) is terminal.
    """
    if code == 429 or 500 <= code <= 599:
        return "retryable"
    return "terminal"


class TTSResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    audio_bytes: bytes
    duration_ms: int
    cost_cents: int
    provider: str
    model: str
    gen_params: dict


class ImageResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    image_bytes: bytes
    cost_cents: int
    provider: str
    model: str
    seed: int
    gen_params: dict


class LLMResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    cost_cents: int
    provider: str
    model: str


class Voice(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    tags: list[str]


@runtime_checkable
class TTSProvider(Protocol):
    name: str

    async def synthesize(
        self, text: str, voice_id: str, emotion: str | None, params: dict
    ) -> TTSResult: ...

    async def list_voices(self) -> list[Voice]: ...

    def estimate_cost_cents(self, text: str) -> int: ...


@runtime_checkable
class ImageProvider(Protocol):
    name: str
    max_reference_images: int

    async def generate(self, prompt: str, seed: int, params: dict) -> ImageResult: ...

    async def generate_from_refs(
        self, prompt: str, refs: list[bytes], seed: int, params: dict
    ) -> ImageResult: ...

    def estimate_cost_cents(self, n: int) -> int: ...


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    async def complete(self, system: str, user: str, params: dict) -> LLMResult: ...

    def estimate_cost_cents(self, prompt_chars: int) -> int: ...
