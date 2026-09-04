"""Unit tests for the shared Google ADC credential plumbing.

No network, no real credentials, and ``google-auth`` never has to be installed:
``GoogleTokenSource`` imports ``google.auth`` *lazily inside* ``_load``, so these
tests stub the whole ``google.auth`` module tree into ``sys.modules``. That is the
same trick the deleted provider tests used on ``aiohttp.ClientSession``, applied
to the auth library instead of the transport — every real code path (ADC
resolution, project fallback, refresh, caching, the lock) still runs.
"""

import asyncio
import sys
import types

import pytest

from app.adapters.base import TerminalProviderError
from app.adapters.google_auth import (
    CLOUD_PLATFORM_SCOPE,
    GoogleTokenSource,
    TokenSource,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must behave identically on a machine with GCP already configured."""
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)


# --------------------------------------------------------------------------
# Fake google-auth
# --------------------------------------------------------------------------


class _DefaultCredentialsError(Exception):
    """Stand-in for google.auth.exceptions.DefaultCredentialsError."""


class _FakeCredentials:
    def __init__(self, token: str | None = "tok-1", valid: bool = False) -> None:
        self.token = token
        self.valid = valid
        self.refreshes = 0

    def refresh(self, request: object) -> None:
        self.refreshes += 1
        self.valid = True
        if self.token is not None:
            self.token = f"{self.token}-refreshed"


def _install_fake_google_auth(
    monkeypatch: pytest.MonkeyPatch,
    *,
    credentials: object = None,
    adc_project: str | None = "adc-project",
    fail: bool = False,
) -> dict:
    """Put a minimal google.auth package into sys.modules and record its use."""
    calls: dict = {"default_scopes": [], "requests": 0}

    google_mod = types.ModuleType("google")
    auth_mod = types.ModuleType("google.auth")
    exceptions_mod = types.ModuleType("google.auth.exceptions")
    transport_mod = types.ModuleType("google.auth.transport")
    requests_mod = types.ModuleType("google.auth.transport.requests")

    exceptions_mod.DefaultCredentialsError = _DefaultCredentialsError  # type: ignore[attr-defined]

    def default(scopes: list[str] | None = None):
        calls["default_scopes"].append(scopes)
        if fail:
            raise _DefaultCredentialsError("could not automatically determine credentials")
        return credentials, adc_project

    class _Request:
        def __init__(self) -> None:
            calls["requests"] += 1

    auth_mod.default = default  # type: ignore[attr-defined]
    auth_mod.exceptions = exceptions_mod  # type: ignore[attr-defined]
    auth_mod.transport = transport_mod  # type: ignore[attr-defined]
    transport_mod.requests = requests_mod  # type: ignore[attr-defined]
    requests_mod.Request = _Request  # type: ignore[attr-defined]
    google_mod.auth = auth_mod  # type: ignore[attr-defined]

    for name, module in (
        ("google", google_mod),
        ("google.auth", auth_mod),
        ("google.auth.exceptions", exceptions_mod),
        ("google.auth.transport", transport_mod),
        ("google.auth.transport.requests", requests_mod),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return calls


# --------------------------------------------------------------------------
# Protocol conformance + construction
# --------------------------------------------------------------------------


def test_token_source_satisfies_protocol() -> None:
    assert isinstance(GoogleTokenSource(), TokenSource)


def test_construction_never_raises_without_credentials() -> None:
    # Must construct fine with no GCP setup at all so offline collection works.
    assert isinstance(GoogleTokenSource(), GoogleTokenSource)
    assert isinstance(GoogleTokenSource(project="p", scopes=("s",)), GoogleTokenSource)


# --------------------------------------------------------------------------
# project()
# --------------------------------------------------------------------------


class TestProject:
    def test_explicit_project_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "env-project")
        assert GoogleTokenSource(project="explicit").project() == "explicit"

    def test_falls_back_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "env-project")
        assert GoogleTokenSource().project() == "env-project"

    def test_unset_is_terminal(self) -> None:
        with pytest.raises(TerminalProviderError) as excinfo:
            GoogleTokenSource().project()
        assert "GOOGLE_CLOUD_PROJECT" in str(excinfo.value)


# --------------------------------------------------------------------------
# token()
# --------------------------------------------------------------------------


async def test_token_resolves_adc_and_refreshes(monkeypatch: pytest.MonkeyPatch) -> None:
    credentials = _FakeCredentials(token="tok", valid=False)
    calls = _install_fake_google_auth(monkeypatch, credentials=credentials)

    source = GoogleTokenSource(project="proj")
    assert await source.token() == "tok-refreshed"
    assert credentials.refreshes == 1
    assert calls["default_scopes"] == [[CLOUD_PLATFORM_SCOPE]]
    assert calls["requests"] == 1


async def test_custom_scopes_are_passed_to_adc(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_google_auth(monkeypatch, credentials=_FakeCredentials(valid=True))
    await GoogleTokenSource(project="proj", scopes=("scope-a", "scope-b")).token()
    assert calls["default_scopes"] == [["scope-a", "scope-b"]]


async def test_valid_credentials_are_not_refreshed(monkeypatch: pytest.MonkeyPatch) -> None:
    credentials = _FakeCredentials(token="already-good", valid=True)
    _install_fake_google_auth(monkeypatch, credentials=credentials)

    assert await GoogleTokenSource(project="proj").token() == "already-good"
    assert credentials.refreshes == 0


async def test_credentials_are_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    credentials = _FakeCredentials(token="tok", valid=False)
    calls = _install_fake_google_auth(monkeypatch, credentials=credentials)

    source = GoogleTokenSource(project="proj")
    first = await source.token()
    second = await source.token()

    # ADC resolved once; the refresh only ran while the token was invalid.
    assert len(calls["default_scopes"]) == 1
    assert credentials.refreshes == 1
    assert first == second == "tok-refreshed"


async def test_concurrent_calls_resolve_credentials_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = _FakeCredentials(token="tok", valid=False)
    calls = _install_fake_google_auth(monkeypatch, credentials=credentials)

    source = GoogleTokenSource(project="proj")
    tokens = await asyncio.gather(*(source.token() for _ in range(8)))

    # The lock serializes the stampede: one ADC resolution, one refresh.
    assert len(calls["default_scopes"]) == 1
    assert credentials.refreshes == 1
    assert set(tokens) == {"tok-refreshed"}


async def test_adc_project_fills_in_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_google_auth(
        monkeypatch, credentials=_FakeCredentials(valid=True), adc_project="from-adc"
    )
    source = GoogleTokenSource()
    with pytest.raises(TerminalProviderError):
        source.project()  # not known until ADC has been resolved
    await source.token()
    assert source.project() == "from-adc"


async def test_explicit_project_survives_adc_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_google_auth(
        monkeypatch, credentials=_FakeCredentials(valid=True), adc_project="from-adc"
    )
    source = GoogleTokenSource(project="explicit")
    await source.token()
    assert source.project() == "explicit"


async def test_projectless_adc_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # A gcloud user login carries no project; the error must name the env var.
    _install_fake_google_auth(
        monkeypatch, credentials=_FakeCredentials(valid=True), adc_project=None
    )
    source = GoogleTokenSource()
    await source.token()
    with pytest.raises(TerminalProviderError) as excinfo:
        source.project()
    assert "GOOGLE_CLOUD_PROJECT" in str(excinfo.value)


# --------------------------------------------------------------------------
# Error paths — all terminal, never retryable
# --------------------------------------------------------------------------


async def test_missing_adc_is_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_google_auth(monkeypatch, fail=True)
    with pytest.raises(TerminalProviderError) as excinfo:
        await GoogleTokenSource(project="proj").token()
    assert "GOOGLE_APPLICATION_CREDENTIALS" in str(excinfo.value)


async def test_missing_google_auth_package_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A None entry in sys.modules makes `import google.auth` raise
    # ModuleNotFoundError, exactly as an uninstalled google-auth would.
    monkeypatch.setitem(sys.modules, "google", None)
    with pytest.raises(TerminalProviderError) as excinfo:
        await GoogleTokenSource(project="proj").token()
    assert "google-auth" in str(excinfo.value)


async def test_credentials_without_token_is_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_google_auth(monkeypatch, credentials=_FakeCredentials(token=None, valid=True))
    with pytest.raises(TerminalProviderError):
        await GoogleTokenSource(project="proj").token()


async def test_token_is_coerced_to_str(monkeypatch: pytest.MonkeyPatch) -> None:
    credentials = _FakeCredentials(valid=True)
    credentials.token = 12345  # type: ignore[assignment]
    _install_fake_google_auth(monkeypatch, credentials=credentials)
    assert await GoogleTokenSource(project="proj").token() == "12345"
