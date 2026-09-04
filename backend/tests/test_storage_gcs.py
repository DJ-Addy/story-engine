"""Unit tests for the GCS media store. No network, no credentials, no bucket.

The HTTP transport is mocked by monkeypatching ``gcs.aiohttp.ClientSession``
with a fake session/response pair implementing the async-context-manager
protocol — the same seam ``test_google_tts`` and ``test_veo`` use — and the ADC
bearer token comes from a stub ``TokenSource`` so ``google-auth`` is never
touched. That exercises the real request shaping, auth headers, status
classification and signing path without leaving the process.

The environment is scrubbed by an autouse fixture: with ``GCS_BUCKET`` unset the
whole subsystem must report itself disabled, which is the state every other test
module in this suite runs in and the state the app runs in locally.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import aiohttp
import pytest

from app.adapters.base import TerminalProviderError
from app.storage import (
    ObjectNotFound,
    ObjectStore,
    ObjectStoreError,
    ObjectStoreUnavailable,
    StorageSettings,
    get_object_store,
    parse_gs_uri,
    reset_object_store_cache,
)
from app.storage import gcs as gcs_module
from app.storage.gcs import GCSObjectStore
from app.storage.settings import DEFAULT_PREFIX, GCS_HOST
from app.storage.signing import ALGORITHM, MAX_EXPIRES_S, build_signing_material

TOKEN = "test-token"
BUCKET = "story-engine-media"
SIGNER = "renderer@story-engine.iam.gserviceaccount.com"
CLOCK = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)

_STORAGE_ENV = (
    "GCS_BUCKET",
    "GCS_PREFIX",
    "GCS_SIGNER_SERVICE_ACCOUNT",
    "GCS_SIGNED_URL_TTL_S",
    "GCS_PUBLIC_BASE_URL",
    "GCS_TIMEOUT_S",
    "STORY_ENGINE_MEDIA_STORAGE_ENABLED",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    """Storage defaults must not depend on the developer's environment."""
    for var in _STORAGE_ENV:
        monkeypatch.delenv(var, raising=False)
    reset_object_store_cache()
    yield
    reset_object_store_cache()


# --------------------------------------------------------------------------
# Fake transport + token source
# --------------------------------------------------------------------------


class _StubTokens:
    """A TokenSource that mints a canned token without google-auth."""

    def __init__(self, token: str = TOKEN, error: Exception | None = None) -> None:
        self._token = token
        self._error = error

    async def token(self) -> str:
        if self._error is not None:
            raise self._error
        return self._token

    def project(self) -> str:
        return "test-project"


class _FakeResponse:
    def __init__(self, status: int, body: object) -> None:
        self.status = status
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def read(self) -> bytes:
        return self._body


class _FakeSession:
    """Stands in for aiohttp.ClientSession; records calls, returns canned bodies."""

    status: int = 200
    body: object = b""
    error: Exception | None = None
    calls: list[dict] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def _record(self, method: str, url: str, **kwargs: object):
        type(self).calls.append({"method": method, "url": url, **kwargs})
        if type(self).error is not None:
            raise type(self).error
        return _FakeResponse(type(self).status, type(self).body)

    def post(self, url: str, data: object = None, json: object = None, headers=None):
        return self._record("POST", url, data=data, json=json, headers=headers)

    def get(self, url: str, headers=None):
        return self._record("GET", url, headers=headers)


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSession]:
    _FakeSession.status = 200
    _FakeSession.body = b""
    _FakeSession.error = None
    _FakeSession.calls = []
    monkeypatch.setattr(gcs_module.aiohttp, "ClientSession", _FakeSession)
    return _FakeSession


def _settings(**overrides: object) -> StorageSettings:
    base: dict = {"enabled": True, "bucket": BUCKET, "prefix": DEFAULT_PREFIX}
    base.update(overrides)
    return StorageSettings(**base)  # type: ignore[arg-type]


def _store(token_error: Exception | None = None, **overrides: object) -> GCSObjectStore:
    return GCSObjectStore(
        _settings(**overrides),
        token_source=_StubTokens(error=token_error),  # type: ignore[arg-type]
        clock=lambda: CLOCK,
    )


# --------------------------------------------------------------------------
# Settings: the unconfigured default and the configured switch
# --------------------------------------------------------------------------


class TestSettingsFromEnv:
    def test_disabled_without_a_bucket(self) -> None:
        settings = StorageSettings.from_env()
        assert settings.enabled is False
        assert settings.bucket == ""

    def test_bucket_name_enables_storage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GCS_BUCKET", BUCKET)
        settings = StorageSettings.from_env()
        assert settings.enabled is True
        assert settings.bucket == BUCKET
        assert settings.prefix == DEFAULT_PREFIX
        assert settings.signer_service_account is None

    def test_gs_uri_form_splits_bucket_and_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GCS_BUCKET", f"gs://{BUCKET}/demo/media/")
        settings = StorageSettings.from_env()
        assert settings.bucket == BUCKET
        assert settings.prefix == "demo/media"

    def test_explicit_prefix_wins_over_uri_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GCS_BUCKET", f"gs://{BUCKET}/ignored")
        monkeypatch.setenv("GCS_PREFIX", "/takes/precedence/")
        assert StorageSettings.from_env().prefix == "takes/precedence"

    def test_empty_prefix_is_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GCS_BUCKET", BUCKET)
        monkeypatch.setenv("GCS_PREFIX", "")
        settings = StorageSettings.from_env()
        assert settings.prefix == ""
        assert settings.audio_object("p1", 2) == "projects/p1/scenes/2/audio.wav"

    def test_force_disable_beats_a_configured_bucket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GCS_BUCKET", BUCKET)
        monkeypatch.setenv("STORY_ENGINE_MEDIA_STORAGE_ENABLED", "0")
        assert StorageSettings.from_env().enabled is False

    def test_signer_and_ttl_and_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GCS_BUCKET", BUCKET)
        monkeypatch.setenv("GCS_SIGNER_SERVICE_ACCOUNT", SIGNER)
        monkeypatch.setenv("GCS_SIGNED_URL_TTL_S", "900")
        monkeypatch.setenv("GCS_PUBLIC_BASE_URL", "https://cdn.example/")
        settings = StorageSettings.from_env()
        assert settings.signer_service_account == SIGNER
        assert settings.signed_url_ttl_s == 900
        assert settings.public_base_url == "https://cdn.example/"
        assert settings.describe()["url_mode"] == "signed"

    def test_garbage_numeric_env_falls_back_to_defaults(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GCS_BUCKET", BUCKET)
        monkeypatch.setenv("GCS_SIGNED_URL_TTL_S", "soon")
        monkeypatch.setenv("GCS_TIMEOUT_S", "later")
        settings = StorageSettings.from_env()
        assert settings.signed_url_ttl_s == 3600
        assert settings.timeout_s == 60.0

    def test_describe_hides_nothing_it_should_not_and_names_the_mode(self) -> None:
        assert _settings().describe()["url_mode"] == "public"


class TestObjectLayout:
    def test_audio_object(self) -> None:
        assert (
            _settings().audio_object("proj-1", 3)
            == "renders/projects/proj-1/scenes/3/audio.wav"
        )

    def test_video_object(self) -> None:
        assert (
            _settings().video_object("proj-1", 3, 2)
            == "renders/projects/proj-1/scenes/3/shots/2/clip.mp4"
        )

    def test_video_prefix_uri_is_a_directory(self) -> None:
        # Veo names the files itself, so storageUri must be a prefix.
        uri = _settings().video_prefix_uri("proj-1", 3, 2)
        assert uri == f"gs://{BUCKET}/renders/projects/proj-1/scenes/3/shots/2/"
        assert uri.endswith("/")

    def test_public_url_defaults_to_the_gcs_host(self) -> None:
        assert (
            _settings().public_url("renders/a b.wav")
            == f"{GCS_HOST}/{BUCKET}/renders/a%20b.wav"
        )

    def test_public_url_honours_a_cdn_base(self) -> None:
        settings = _settings(public_base_url="https://cdn.example/")
        assert settings.public_url("x/y.wav") == f"https://cdn.example/{BUCKET}/x/y.wav"


class TestParseGsUri:
    def test_splits_bucket_and_object(self) -> None:
        assert parse_gs_uri("gs://b/a/c.mp4") == ("b", "a/c.mp4")

    @pytest.mark.parametrize(
        "uri", ["https://b/a.mp4", "gs://", "gs://bucket", "gs://bucket/", "b/a.mp4"]
    )
    def test_rejects_anything_that_is_not_an_object_uri(self, uri: str) -> None:
        with pytest.raises(ValueError):
            parse_gs_uri(uri)


# --------------------------------------------------------------------------
# The factory: None when unconfigured, cached when configured
# --------------------------------------------------------------------------


class TestGetObjectStore:
    def test_returns_none_without_a_bucket(self) -> None:
        assert get_object_store() is None

    def test_returns_none_when_force_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GCS_BUCKET", BUCKET)
        monkeypatch.setenv("STORY_ENGINE_MEDIA_STORAGE_ENABLED", "no")
        assert get_object_store() is None

    def test_returns_a_gcs_store_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GCS_BUCKET", BUCKET)
        store = get_object_store()
        assert isinstance(store, GCSObjectStore)
        assert isinstance(store, ObjectStore)
        assert store.settings.bucket == BUCKET

    def test_caches_so_adc_is_not_re_resolved_per_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GCS_BUCKET", BUCKET)
        assert get_object_store() is get_object_store()

    def test_reconfiguration_yields_a_different_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GCS_BUCKET", BUCKET)
        first = get_object_store()
        monkeypatch.setenv("GCS_BUCKET", "other-bucket")
        assert get_object_store() is not first

    def test_reset_clears_the_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GCS_BUCKET", BUCKET)
        first = get_object_store()
        reset_object_store_cache()
        assert get_object_store() is not first

    def test_construction_never_touches_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Offline collection law: building the store must not resolve ADC.
        monkeypatch.setenv("GCS_BUCKET", BUCKET)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        assert get_object_store() is not None


# --------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------


class TestUpload:
    @pytest.mark.asyncio
    async def test_posts_media_and_returns_the_gs_uri(
        self, fake_session: type[_FakeSession]
    ) -> None:
        fake_session.body = {"name": "renders/x.wav"}
        uri = await _store().upload("renders/x.wav", b"RIFFDATA", "audio/wav")

        assert uri == f"gs://{BUCKET}/renders/x.wav"
        call = fake_session.calls[-1]
        assert call["method"] == "POST"
        assert call["url"].startswith(f"{GCS_HOST}/upload/storage/v1/b/{BUCKET}/o?")
        assert "uploadType=media" in call["url"]
        # The object name is a path, so every slash must be escaped in the query.
        assert "name=renders%2Fx.wav" in call["url"]
        assert call["data"] == b"RIFFDATA"
        assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"
        assert call["headers"]["Content-Type"] == "audio/wav"

    @pytest.mark.asyncio
    async def test_default_content_type_is_opaque(
        self, fake_session: type[_FakeSession]
    ) -> None:
        await _store().upload("x", b"1")
        assert fake_session.calls[-1]["headers"]["Content-Type"] == "application/octet-stream"

    @pytest.mark.asyncio
    async def test_auth_failure_is_terminal(self, fake_session: type[_FakeSession]) -> None:
        store = _store(token_error=TerminalProviderError("no ADC here"))
        with pytest.raises(ObjectStoreError, match="gcs auth failed") as exc:
            await store.upload("x", b"1")
        assert not isinstance(exc.value, ObjectStoreUnavailable)
        assert fake_session.calls == []  # never even opened a session

    @pytest.mark.asyncio
    async def test_403_is_terminal(self, fake_session: type[_FakeSession]) -> None:
        fake_session.status = 403
        fake_session.body = b'{"error": "forbidden"}'
        with pytest.raises(ObjectStoreError, match="gcs upload http 403") as exc:
            await _store().upload("x", b"1")
        assert not isinstance(exc.value, (ObjectNotFound, ObjectStoreUnavailable))

    @pytest.mark.asyncio
    async def test_404_means_the_bucket_is_missing(
        self, fake_session: type[_FakeSession]
    ) -> None:
        fake_session.status = 404
        with pytest.raises(ObjectNotFound):
            await _store().upload("x", b"1")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [429, 500, 503])
    async def test_retryable_statuses(
        self, fake_session: type[_FakeSession], status: int
    ) -> None:
        fake_session.status = status
        with pytest.raises(ObjectStoreUnavailable):
            await _store().upload("x", b"1")

    @pytest.mark.asyncio
    async def test_network_failure_is_retryable(
        self, fake_session: type[_FakeSession]
    ) -> None:
        fake_session.error = aiohttp.ClientError("connection reset")
        with pytest.raises(ObjectStoreUnavailable, match="network failure"):
            await _store().upload("x", b"1")


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------


class TestFetch:
    @pytest.mark.asyncio
    async def test_reads_the_object_bytes_back(
        self, fake_session: type[_FakeSession]
    ) -> None:
        fake_session.body = b"RIFFDATA"
        data = await _store().fetch("renders/x.wav")

        assert data == b"RIFFDATA"
        call = fake_session.calls[-1]
        assert call["method"] == "GET"
        assert call["url"] == (
            f"{GCS_HOST}/storage/v1/b/{BUCKET}/o/renders%2Fx.wav?alt=media"
        )
        assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"
        assert "Content-Type" not in call["headers"]

    @pytest.mark.asyncio
    async def test_missing_object_is_object_not_found(
        self, fake_session: type[_FakeSession]
    ) -> None:
        fake_session.status = 404
        fake_session.body = b'{"error": "No such object"}'
        with pytest.raises(ObjectNotFound, match="gcs fetch http 404"):
            await _store().fetch("renders/gone.wav")

    @pytest.mark.asyncio
    async def test_server_error_is_retryable(
        self, fake_session: type[_FakeSession]
    ) -> None:
        fake_session.status = 500
        with pytest.raises(ObjectStoreUnavailable):
            await _store().fetch("renders/x.wav")

    @pytest.mark.asyncio
    async def test_auth_failure_is_terminal(self, fake_session: type[_FakeSession]) -> None:
        store = _store(token_error=TerminalProviderError("no ADC here"))
        with pytest.raises(ObjectStoreError, match="gcs auth failed"):
            await store.fetch("renders/x.wav")

    @pytest.mark.asyncio
    async def test_network_failure_is_retryable(
        self, fake_session: type[_FakeSession]
    ) -> None:
        fake_session.error = TimeoutError("too slow")
        with pytest.raises(ObjectStoreUnavailable, match="network failure"):
            await _store().fetch("renders/x.wav")

    @pytest.mark.asyncio
    async def test_fetch_uri_reads_the_bucket_named_in_the_uri(
        self, fake_session: type[_FakeSession]
    ) -> None:
        # A provider-written clip may live in a bucket other than ours.
        fake_session.body = b"MP4"
        data = await _store().fetch_uri("gs://veo-out/a/b/clip.mp4")
        assert data == b"MP4"
        assert fake_session.calls[-1]["url"] == (
            f"{GCS_HOST}/storage/v1/b/veo-out/o/a%2Fb%2Fclip.mp4?alt=media"
        )

    @pytest.mark.asyncio
    async def test_fetch_uri_rejects_a_prefix(
        self, fake_session: type[_FakeSession]
    ) -> None:
        with pytest.raises(ObjectStoreError, match="names no object"):
            await _store().fetch_uri("gs://veo-out/")
        assert fake_session.calls == []


# --------------------------------------------------------------------------
# Serving URLs: public, and V4-signed via IAM signBlob
# --------------------------------------------------------------------------


class TestSigningMaterial:
    def test_layout_is_the_v4_canonical_form(self) -> None:
        string_to_sign, url = build_signing_material(
            BUCKET,
            "renders/a.wav",
            service_account=SIGNER,
            timestamp=CLOCK,
            expires_s=900,
        )
        lines = string_to_sign.split("\n")
        assert lines[0] == ALGORITHM
        assert lines[1] == "20260903T120000Z"
        assert lines[2] == "20260903/auto/storage/goog4_request"
        assert len(lines[3]) == 64  # sha256 hex of the canonical request
        assert url.startswith(f"https://storage.googleapis.com/{BUCKET}/renders/a.wav?")
        assert "X-Goog-Expires=900" in url
        assert f"X-Goog-Algorithm={ALGORITHM}" in url
        assert "X-Goog-SignedHeaders=host" in url

    def test_query_is_sorted_and_encoded(self) -> None:
        _, url = build_signing_material(
            BUCKET, "a.wav", service_account=SIGNER, timestamp=CLOCK, expires_s=60
        )
        query = url.split("?", 1)[1]
        keys = [pair.split("=")[0] for pair in query.split("&")]
        assert keys == sorted(keys)
        assert "%40" in query  # the signer's "@" is percent-encoded in the credential

    def test_is_deterministic_for_a_fixed_timestamp(self) -> None:
        args = dict(service_account=SIGNER, timestamp=CLOCK, expires_s=60)
        assert build_signing_material(BUCKET, "a.wav", **args) == build_signing_material(
            BUCKET, "a.wav", **args
        )

    def test_expiry_is_clamped_to_the_seven_day_maximum(self) -> None:
        _, url = build_signing_material(
            BUCKET, "a.wav", service_account=SIGNER, timestamp=CLOCK, expires_s=10**9
        )
        assert f"X-Goog-Expires={MAX_EXPIRES_S}" in url

    def test_requires_a_signer(self) -> None:
        with pytest.raises(ValueError):
            build_signing_material(
                BUCKET, "a.wav", service_account="", timestamp=CLOCK, expires_s=60
            )


class TestServingUrl:
    @pytest.mark.asyncio
    async def test_public_when_no_signer_is_configured(
        self, fake_session: type[_FakeSession]
    ) -> None:
        url = await _store().serving_url("renders/a.wav")
        assert url == f"{GCS_HOST}/{BUCKET}/renders/a.wav"
        assert fake_session.calls == []  # a public URL costs no request

    @pytest.mark.asyncio
    async def test_signed_url_calls_sign_blob_and_appends_the_signature(
        self, fake_session: type[_FakeSession]
    ) -> None:
        signature = b"\xde\xad\xbe\xef"
        fake_session.body = {"signedBlob": base64.b64encode(signature).decode("ascii")}
        store = _store(signer_service_account=SIGNER, signed_url_ttl_s=900)

        url = await store.signed_url("renders/a.wav")

        expected, base_url = build_signing_material(
            BUCKET,
            "renders/a.wav",
            service_account=SIGNER,
            timestamp=CLOCK,
            expires_s=900,
        )
        assert url == f"{base_url}&X-Goog-Signature=deadbeef"

        call = fake_session.calls[-1]
        assert call["url"] == (
            "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
            f"{SIGNER}:signBlob"
        )
        assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"
        # signBlob signs exactly the V4 string-to-sign, base64-wrapped.
        assert base64.b64decode(call["json"]["payload"]).decode("utf-8") == expected

    @pytest.mark.asyncio
    async def test_serving_url_prefers_a_signed_url_when_a_signer_exists(
        self, fake_session: type[_FakeSession]
    ) -> None:
        fake_session.body = {"signedBlob": base64.b64encode(b"\x01\x02").decode("ascii")}
        url = await _store(signer_service_account=SIGNER).serving_url("renders/a.wav")
        assert "X-Goog-Signature=0102" in url

    @pytest.mark.asyncio
    async def test_explicit_expiry_overrides_the_configured_ttl(
        self, fake_session: type[_FakeSession]
    ) -> None:
        fake_session.body = {"signedBlob": base64.b64encode(b"\x01").decode("ascii")}
        store = _store(signer_service_account=SIGNER, signed_url_ttl_s=3600)
        url = await store.signed_url("renders/a.wav", expires_s=120)
        assert "X-Goog-Expires=120" in url

    @pytest.mark.asyncio
    async def test_signing_without_a_signer_is_a_clear_error(
        self, fake_session: type[_FakeSession]
    ) -> None:
        with pytest.raises(ObjectStoreError, match="GCS_SIGNER_SERVICE_ACCOUNT"):
            await _store().signed_url("renders/a.wav")
        assert fake_session.calls == []

    @pytest.mark.asyncio
    async def test_sign_blob_permission_denied_is_terminal(
        self, fake_session: type[_FakeSession]
    ) -> None:
        fake_session.status = 403
        fake_session.body = b'{"error": "iam.serviceAccounts.signBlob denied"}'
        with pytest.raises(ObjectStoreError, match="gcs signBlob http 403") as exc:
            await _store(signer_service_account=SIGNER).signed_url("renders/a.wav")
        assert not isinstance(exc.value, ObjectStoreUnavailable)

    @pytest.mark.asyncio
    async def test_sign_blob_server_error_is_retryable(
        self, fake_session: type[_FakeSession]
    ) -> None:
        fake_session.status = 503
        with pytest.raises(ObjectStoreUnavailable):
            await _store(signer_service_account=SIGNER).signed_url("renders/a.wav")

    @pytest.mark.asyncio
    async def test_sign_blob_without_a_signature_is_an_error(
        self, fake_session: type[_FakeSession]
    ) -> None:
        fake_session.body = {"nothing": "here"}
        with pytest.raises(ObjectStoreError, match="no signature"):
            await _store(signer_service_account=SIGNER).signed_url("renders/a.wav")

    @pytest.mark.asyncio
    async def test_sign_blob_non_json_body_is_an_error(
        self, fake_session: type[_FakeSession]
    ) -> None:
        fake_session.body = b"<html>not json</html>"
        with pytest.raises(ObjectStoreError, match="non-JSON"):
            await _store(signer_service_account=SIGNER).signed_url("renders/a.wav")

    @pytest.mark.asyncio
    async def test_sign_blob_network_failure_is_retryable(
        self, fake_session: type[_FakeSession]
    ) -> None:
        fake_session.error = aiohttp.ClientError("boom")
        with pytest.raises(ObjectStoreUnavailable, match="network failure"):
            await _store(signer_service_account=SIGNER).signed_url("renders/a.wav")
