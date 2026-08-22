"""Tests for retry orchestration and idempotency keys."""

import pytest

from app.adapters.base import RetryableProviderError, TerminalProviderError
from app.costs.retry import idempotency_key, run_with_retries


class CallCounter:
    """Async callable that fails a configurable number of times before succeeding."""

    def __init__(
        self,
        fail_times: int = 0,
        exc: type[Exception] = RetryableProviderError,
        result: str = "ok",
    ) -> None:
        self.calls = 0
        self._fail_times = fail_times
        self._exc = exc
        self._result = result

    async def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._exc(f"injected failure #{self.calls}")
        return self._result


class TestRunWithRetries:
    async def test_success_first_try(self) -> None:
        fn = CallCounter()
        assert await run_with_retries(fn, base_delay_s=0.0) == "ok"
        assert fn.calls == 1

    async def test_succeeds_after_two_retryable_failures(self) -> None:
        fn = CallCounter(fail_times=2)
        assert await run_with_retries(fn, max_attempts=3, base_delay_s=0.0) == "ok"
        assert fn.calls == 3

    async def test_terminal_error_raises_immediately_one_call(self) -> None:
        fn = CallCounter(fail_times=10, exc=TerminalProviderError)
        with pytest.raises(TerminalProviderError):
            await run_with_retries(fn, max_attempts=3, base_delay_s=0.0)
        assert fn.calls == 1

    async def test_retryable_exhaustion_raises_after_max_attempts(self) -> None:
        fn = CallCounter(fail_times=10)
        with pytest.raises(RetryableProviderError):
            await run_with_retries(fn, max_attempts=3, base_delay_s=0.0)
        assert fn.calls == 3

    async def test_max_attempts_one_no_retry(self) -> None:
        fn = CallCounter(fail_times=1)
        with pytest.raises(RetryableProviderError):
            await run_with_retries(fn, max_attempts=1, base_delay_s=0.0)
        assert fn.calls == 1


class TestIdempotencyKey:
    def test_stable_under_key_reordering(self) -> None:
        a = idempotency_key("tts", {"text": "hi", "voice": "v1", "seed": 3})
        b = idempotency_key("tts", {"seed": 3, "voice": "v1", "text": "hi"})
        assert a == b

    def test_differs_for_different_payloads(self) -> None:
        a = idempotency_key("tts", {"text": "hi"})
        b = idempotency_key("tts", {"text": "bye"})
        assert a != b

    def test_differs_for_different_kinds(self) -> None:
        a = idempotency_key("tts", {"text": "hi"})
        b = idempotency_key("image", {"text": "hi"})
        assert a != b

    def test_stable_with_nested_dicts(self) -> None:
        a = idempotency_key("job", {"params": {"x": 1, "y": 2}, "id": "a"})
        b = idempotency_key("job", {"id": "a", "params": {"y": 2, "x": 1}})
        assert a == b

    def test_is_hex_sha256(self) -> None:
        key = idempotency_key("tts", {"text": "hi"})
        assert len(key) == 64
        int(key, 16)  # raises if not valid hex
