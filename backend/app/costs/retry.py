"""Retry orchestration and idempotency keys for provider calls."""

import asyncio
import hashlib
import json
import random
from collections.abc import Awaitable, Callable

from app.adapters.base import RetryableProviderError, TerminalProviderError


async def run_with_retries[T](
    fn: Callable[[], Awaitable[T]],
    max_attempts: int = 3,
    base_delay_s: float = 0.0,
    jitter: bool = False,
) -> T:
    """Run fn, retrying RetryableProviderError with exponential backoff.

    TerminalProviderError is re-raised immediately and never retried.
    base_delay_s=0.0 makes tests instant; production callers pass a real delay.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except TerminalProviderError:
            raise
        except RetryableProviderError:
            if attempt >= max_attempts:
                raise
            delay = base_delay_s * (2 ** (attempt - 1))
            if jitter:
                delay *= random.uniform(0.5, 1.5)
            if delay > 0:
                await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # loop always returns or raises


def idempotency_key(kind: str, payload: dict) -> str:
    """Stable sha256 key over kind + canonical JSON of payload.

    Canonical form uses sorted keys and compact separators, so the key is
    identical regardless of dict insertion order (including nested dicts).
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{kind}:{canonical}".encode("utf-8")).hexdigest()
